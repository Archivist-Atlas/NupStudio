"""NUP Explorer tab widget: zip-like browser, mutator, and asset previewer."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .media_preview import AssetPreviewPanel
from .nup_explorer import (
    NupMutation,
    NupNodeEntry,
    NupPackageSummary,
    extract_nup_entry,
    load_nup_tree,
    mutate_nup,
    read_nup_file_bytes,
)


def _fmt_size(n: int) -> str:
    val = float(n)
    for unit in ("o", "Ko", "Mo", "Go"):
        if val < 1024.0:
            return f"{val:.1f} {unit}" if unit != "o" else f"{int(val)} o"
        val /= 1024.0
    return f"{val:.2f} To"


class _ExtractWorker(QThread):
    done = Signal(bool, str)

    def __init__(
        self,
        root_dir: Path,
        nup_path: Path,
        pkg_idx: int,
        rel_parts: tuple[str, ...],
        is_dir: bool,
        dest: Path,
    ) -> None:
        super().__init__()
        self.root_dir = root_dir
        self.nup_path = nup_path
        self.pkg_idx = pkg_idx
        self.rel_parts = rel_parts
        self.is_dir = is_dir
        self.dest = dest

    def run(self) -> None:
        try:
            n = extract_nup_entry(
                self.root_dir,
                self.nup_path,
                self.pkg_idx,
                self.rel_parts,
                self.is_dir,
                self.dest,
            )
            if self.is_dir:
                self.done.emit(True, f"{n} fichier(s) extrait(s) dans :\n{self.dest}")
            else:
                self.done.emit(True, f"Fichier extrait :\n{self.dest}")
        except Exception as exc:
            self.done.emit(False, str(exc))


class _MutateWorker(QThread):
    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, root_dir: Path, nup_path: Path, ops: list[NupMutation], ok_msg: str) -> None:
        super().__init__()
        self.root_dir = root_dir
        self.nup_path = nup_path
        self.ops = ops
        self.ok_msg = ok_msg

    def run(self) -> None:
        try:
            mutate_nup(
                self.root_dir,
                self.nup_path,
                self.ops,
                progress_cb=lambda cur, tot: self.progress.emit(cur, tot),
            )
            self.done.emit(True, self.ok_msg)
        except Exception as exc:
            self.done.emit(False, str(exc))


class NupExplorerTab(QWidget):
    """Tab widget for browsing, modding, and previewing assets inside a .nup file."""

    def __init__(self, root_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TabPage")
        self.root_dir = root_dir
        self.current_nup: Optional[Path] = None
        self._packages: list[NupPackageSummary] = []
        self._entries_by_pkg: dict[int, list[NupNodeEntry]] = {}
        self._mut_worker: Optional[_MutateWorker] = None
        self._ext_worker: Optional[_ExtractWorker] = None
        self._clipboard_copy: Optional[tuple[int, tuple[str, ...], str]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Top file bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.nup_input = QLineEdit()
        self.nup_input.setPlaceholderText("Ouvrez un fichier .nup pour explorer son contenu…")
        self.nup_input.setReadOnly(True)
        btn_open = QPushButton("Ouvrir .nup…")
        btn_open.setObjectName("PrimaryBtn")
        btn_open.clicked.connect(self._browse_nup)
        self.btn_reload = QPushButton("Actualiser")
        self.btn_reload.setEnabled(False)
        self.btn_reload.clicked.connect(self._reload_tree)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filtrer par nom…")
        self.search_input.setMaximumWidth(220)
        self.search_input.textChanged.connect(self._apply_filter)
        top_bar.addWidget(self.nup_input, stretch=1)
        top_bar.addWidget(self.search_input)
        top_bar.addWidget(self.btn_reload)
        top_bar.addWidget(btn_open)
        layout.addLayout(top_bar)

        # Action toolbar
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_extract = QPushButton("Extraire…")
        self.btn_mod_update = QPushButton("Modder (via Update)…")
        self.btn_mod_update.setObjectName("PrimaryBtn")
        self.btn_mod_update.setToolTip(
            "Remplace/ajoute le fichier dans un package update dédié sans écraser les données originales du jeu."
        )
        self.btn_import = QPushButton("Importer / Remplacer…")
        self.btn_copy_in = QPushButton("Copier")
        self.btn_paste_in = QPushButton("Coller ici")
        self.btn_rename = QPushButton("Renommer")
        self.btn_delete = QPushButton("Supprimer")

        self.btn_extract.clicked.connect(self._extract_selected)
        self.btn_mod_update.clicked.connect(self._mod_into_update)
        self.btn_import.clicked.connect(self._import_into_selected)
        self.btn_copy_in.clicked.connect(self._copy_selected)
        self.btn_paste_in.clicked.connect(self._paste_into_selected)
        self.btn_rename.clicked.connect(self._rename_selected)
        self.btn_delete.clicked.connect(self._delete_selected)

        for b in (
            self.btn_extract,
            self.btn_mod_update,
            self.btn_import,
            self.btn_copy_in,
            self.btn_paste_in,
            self.btn_rename,
            self.btn_delete,
        ):
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Main splitter: Tree (left) + Asset Previewer (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Nom", "Taille", "Codec"])
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setStyleSheet(
            "QTreeWidget { background-color: #0f1520; border: 1px solid #1e293b; border-radius: 10px; color: #f1f5f9; }"
            "QTreeWidget::item:selected { background-color: #1e293b; color: #ffffff; }"
        )
        self.tree.currentItemChanged.connect(self._on_item_selected)
        splitter.addWidget(self.tree)

        self.preview = AssetPreviewPanel()
        splitter.addWidget(self.preview)
        splitter.setSizes([470, 470])
        layout.addWidget(splitter, stretch=1)

        self.lbl_status = QLabel("Aucun fichier .nup ouvert.")
        self.lbl_status.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self.lbl_status)

    def _browse_nup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir un conteneur .nup",
            "",
            "Fichiers NUP (*.nup);;Tous les fichiers (*)",
        )
        if path:
            self.open_nup(Path(path))

    def open_nup(self, nup_path: Path) -> None:
        self.current_nup = nup_path
        self.nup_input.setText(str(nup_path))
        self.btn_reload.setEnabled(True)
        self._reload_tree()

    def _reload_tree(self) -> None:
        if self.current_nup is None or not self.current_nup.is_file():
            return
        try:
            self._packages, self._entries_by_pkg = load_nup_tree(self.root_dir, self.current_nup)
        except Exception as exc:
            QMessageBox.warning(self, "Erreur de lecture .nup", str(exc))
            return
        self._populate_tree()
        total_files = sum(
            1 for lst in self._entries_by_pkg.values() for e in lst if not e.is_dir
        )
        self.lbl_status.setText(
            f"{self.current_nup.name} — {len(self._packages)} package(s), {total_files:,} fichier(s)"
        )

    def _populate_tree(self) -> None:
        self.tree.clear()
        self.preview.clear()
        flt = self.search_input.text().strip().lower()

        for pkg in self._packages:
            pkg_item = QTreeWidgetItem(
                [f"[{pkg.index}] {pkg.kind_name} ({pkg.title_id} {pkg.version_label})", "", ""]
            )
            pkg_item.setData(0, Qt.ItemDataRole.UserRole, (pkg.index, (), True, True))
            self.tree.addTopLevelItem(pkg_item)

            dir_items: dict[tuple[str, ...], QTreeWidgetItem] = {(): pkg_item}
            entries = self._entries_by_pkg.get(pkg.index, [])
            for entry in entries:
                if flt and not entry.is_dir and flt not in entry.name.lower():
                    continue
                parent_parts = entry.rel_parts[:-1]
                # Ensure parent dir items exist even when filtering
                cur_parent = pkg_item
                acc: tuple[str, ...] = ()
                for part in parent_parts:
                    acc = acc + (part,)
                    if acc not in dir_items:
                        di = QTreeWidgetItem([part, "", "dossier"])
                        di.setData(0, Qt.ItemDataRole.UserRole, (pkg.index, acc, True, False))
                        cur_parent.addChild(di)
                        dir_items[acc] = di
                    cur_parent = dir_items[acc]

                if entry.is_dir:
                    if not flt and entry.rel_parts not in dir_items:
                        di = QTreeWidgetItem([entry.name, "", "dossier"])
                        di.setData(
                            0, Qt.ItemDataRole.UserRole, (pkg.index, entry.rel_parts, True, False)
                        )
                        cur_parent.addChild(di)
                        dir_items[entry.rel_parts] = di
                else:
                    fi = QTreeWidgetItem([entry.name, _fmt_size(entry.size), entry.codec])
                    fi.setData(
                        0, Qt.ItemDataRole.UserRole, (pkg.index, entry.rel_parts, False, False)
                    )
                    cur_parent.addChild(fi)

            pkg_item.setExpanded(True)
        self._update_buttons()

    def _apply_filter(self) -> None:
        if self._packages:
            self._populate_tree()

    def _selected_meta(self) -> Optional[tuple[int, tuple[str, ...], bool, bool]]:
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _update_buttons(self) -> None:
        meta = self._selected_meta()
        busy = self._mut_worker is not None or self._ext_worker is not None
        has_sel = meta is not None and not busy
        is_pkg_root = bool(meta[3]) if meta else False
        is_dir = bool(meta[2]) if meta else False

        self.btn_extract.setEnabled(has_sel)
        self.btn_mod_update.setEnabled(has_sel)
        self.btn_import.setEnabled(has_sel)
        self.btn_copy_in.setEnabled(has_sel and not is_dir)
        self.btn_paste_in.setEnabled(has_sel and self._clipboard_copy is not None)
        self.btn_rename.setEnabled(has_sel and not is_pkg_root)
        can_del_pkg = is_pkg_root and len(self._packages) > 1 and (meta[0] > 0 if meta else False)
        self.btn_delete.setEnabled(has_sel and (not is_pkg_root or can_del_pkg))

    def _on_item_selected(self, current: Optional[QTreeWidgetItem], _prev) -> None:
        self._update_buttons()
        if current is None or self.current_nup is None:
            return
        meta = current.data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return
        pkg_idx, rel_parts, is_dir, _is_pkg = meta
        if is_dir:
            self.preview.clear()
            return
        try:
            data = read_nup_file_bytes(self.root_dir, self.current_nup, pkg_idx, rel_parts)
            self.preview.preview_bytes(rel_parts[-1], data)
        except Exception as exc:
            self.lbl_status.setText(f"Erreur lecture {rel_parts[-1]} : {exc}")

    def _extract_selected(self) -> None:
        meta = self._selected_meta()
        if not meta or self.current_nup is None or self._ext_worker is not None:
            return
        pkg_idx, rel_parts, is_dir, _is_pkg = meta
        if is_dir:
            dest_dir = QFileDialog.getExistingDirectory(self, "Dossier d'extraction")
            if not dest_dir:
                return
            target = Path(dest_dir) / rel_parts[-1] if rel_parts else Path(dest_dir)
        else:
            dest_file, _ = QFileDialog.getSaveFileName(
                self, "Extraire le fichier", rel_parts[-1], "Tous les fichiers (*)"
            )
            if not dest_file:
                return
            target = Path(dest_file)

        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        worker = _ExtractWorker(
            self.root_dir, self.current_nup, pkg_idx, rel_parts, is_dir, target
        )
        worker.done.connect(self._on_extract_done)
        self._ext_worker = worker
        self._update_buttons()
        worker.start()

    def _on_extract_done(self, ok: bool, msg: str) -> None:
        self._ext_worker = None
        self.progress.setVisible(False)
        self.progress.setRange(0, 1)
        self._update_buttons()
        if ok:
            QMessageBox.information(self, "Extraction terminée", msg)
        else:
            QMessageBox.warning(self, "Erreur d'extraction", msg)

    def _run_mutation(self, ops: list[NupMutation], ok_msg: str) -> None:
        if self.current_nup is None or self._mut_worker is not None:
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        worker = _MutateWorker(self.root_dir, self.current_nup, ops, ok_msg)
        worker.progress.connect(
            lambda cur, tot: self.progress.setValue(int(cur * 100 / tot) if tot else 100)
        )
        worker.done.connect(self._on_mutation_done)
        self._mut_worker = worker
        self._update_buttons()
        worker.start()

    def _on_mutation_done(self, ok: bool, msg: str) -> None:
        self._mut_worker = None
        self.progress.setVisible(False)
        if ok:
            self._reload_tree()
            QMessageBox.information(self, "Modification NUP terminée", msg)
        else:
            self._update_buttons()
            QMessageBox.warning(self, "Erreur de modification NUP", msg)

    def _mod_into_update(self) -> None:
        meta = self._selected_meta()
        if not meta:
            return
        _pkg_idx, rel_parts, is_dir, _is_pkg = meta
        local_file, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner le fichier moddé à placer dans le package Update",
            "",
            "Tous les fichiers (*)",
        )
        if not local_file:
            return
        src = Path(local_file)
        if is_dir:
            dest_dir = rel_parts
            target_name = src.name
        else:
            dest_dir = rel_parts[:-1]
            target_name = rel_parts[-1]
        self._run_mutation(
            [NupMutation("mod_import", 0, dest_dir, new_name=target_name, local_path=src)],
            f"Mod appliqué dans le package update : {'/'.join(dest_dir + (target_name,))}\n"
            "(Les données originales du jeu restent intactes.)",
        )

    def _import_into_selected(self) -> None:
        meta = self._selected_meta()
        if not meta:
            return
        pkg_idx, rel_parts, is_dir, _is_pkg = meta
        local_file, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner le fichier à importer / remplacer", "", "Tous les fichiers (*)"
        )
        if not local_file:
            return
        src = Path(local_file)
        if is_dir:
            dest_dir = rel_parts
            target_name = src.name
        else:
            dest_dir = rel_parts[:-1]
            target_name = rel_parts[-1]
        self._run_mutation(
            [NupMutation("import", pkg_idx, dest_dir, new_name=target_name, local_path=src)],
            f"Fichier '{target_name}' importé/remplacé avec succès.",
        )

    def _copy_selected(self) -> None:
        meta = self._selected_meta()
        if not meta or meta[2]:
            return
        pkg_idx, rel_parts, _is_dir, _is_pkg = meta
        self._clipboard_copy = (pkg_idx, rel_parts, rel_parts[-1])
        self.lbl_status.setText(f"Copié en mémoire : {'/'.join(rel_parts)}")
        self._update_buttons()

    def _paste_into_selected(self) -> None:
        if self._clipboard_copy is None:
            return
        meta = self._selected_meta()
        if not meta:
            return
        src_pkg, src_parts, default_name = self._clipboard_copy
        dest_pkg, dest_parts, is_dir, _is_pkg = meta
        target_dir = dest_parts if is_dir else dest_parts[:-1]
        new_name, ok = QInputDialog.getText(
            self, "Coller dans le .nup", "Nom du fichier collé :", text=default_name
        )
        if not ok or not new_name.strip():
            return
        self._run_mutation(
            [
                NupMutation(
                    "copy",
                    src_pkg,
                    src_parts,
                    new_name=new_name.strip(),
                    dest_pkg_index=dest_pkg,
                    dest_dir_parts=target_dir,
                )
            ],
            f"Fichier collé sous '{new_name.strip()}'.",
        )

    def _rename_selected(self) -> None:
        meta = self._selected_meta()
        if not meta or meta[3]:
            return
        pkg_idx, rel_parts, _is_dir, _is_pkg = meta
        new_name, ok = QInputDialog.getText(
            self, "Renommer", "Nouveau nom :", text=rel_parts[-1]
        )
        if not ok or not new_name.strip() or new_name.strip() == rel_parts[-1]:
            return
        self._run_mutation(
            [NupMutation("rename", pkg_idx, rel_parts, new_name=new_name.strip())],
            f"Renommé en '{new_name.strip()}'.",
        )

    def _delete_selected(self) -> None:
        meta = self._selected_meta()
        if not meta:
            return
        pkg_idx, rel_parts, _is_dir, is_pkg = meta
        if is_pkg:
            if len(self._packages) <= 1:
                return
            ans = QMessageBox.question(
                self,
                "Supprimer le package",
                f"Supprimer entièrement le package [{pkg_idx}] du fichier .nup ?",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            self._run_mutation(
                [NupMutation("delete_package", pkg_idx, ())],
                f"Package [{pkg_idx}] supprimé.",
            )
            return
        ans = QMessageBox.question(
            self,
            "Confirmer la suppression",
            f"Supprimer '{'/'.join(rel_parts)}' du fichier .nup ?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._run_mutation(
            [NupMutation("delete", pkg_idx, rel_parts)],
            f"'{rel_parts[-1]}' supprimé.",
        )
