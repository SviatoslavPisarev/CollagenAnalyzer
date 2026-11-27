"""Fiber model page: paint fiber / background strokes on a few images and train a pixel classifier."""
import os

import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QSettings, QStandardPaths, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QKeySequence, QShortcut, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QGroupBox, QLabel, QMessageBox, QRadioButton, QScrollArea, QSlider,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

import collagen_core as core
from ml import fibermodel as FM
from .page_params import Task
from .widgets import Page, button, hint, banner, hbox
from . import style

FIBER, BACKGROUND = QColor(255, 96, 0), QColor(0, 150, 255)
IMAGE_EXT = (".nd2", ".tif", ".tiff")


def _gray_qimage(a):
    a = np.ascontiguousarray(a, np.uint8)
    h, w = a.shape
    return QImage(a.data, w, h, w, QImage.Format_Grayscale8).copy()


def _rgba_qimage(rgba):
    rgba = np.ascontiguousarray(rgba, np.uint8)
    h, w, _ = rgba.shape
    return QImage(rgba.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()


def labels_to_qimage(lab):
    rgba = np.zeros(lab.shape + (4,), np.uint8)
    for v, c in ((1, FIBER), (2, BACKGROUND)):
        m = lab == v
        rgba[m] = (c.red(), c.green(), c.blue(), 255)
    return _rgba_qimage(rgba).convertToFormat(QImage.Format_ARGB32)


def qimage_to_labels(img):
    img = img.convertToFormat(QImage.Format_RGBA8888)
    h, w = img.height(), img.width()
    a = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())[:, :4 * w]
    a = a.reshape(h, w, 4)
    lab = np.zeros((h, w), np.uint8)
    on = a[..., 3] > 0
    lab[on & (a[..., 0] > a[..., 2])] = 1
    lab[on & (a[..., 2] >= a[..., 0])] = 2
    return lab


class Canvas(QGraphicsView):
    """Image with a paintable label layer and an optional mask overlay. Left = paint, right/middle drag = pan,
    wheel = zoom."""
    painted = Signal()

    def __init__(self):
        super().__init__()
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.img_item = QGraphicsPixmapItem()
        self.ovl_item = QGraphicsPixmapItem()
        self.lab_item = QGraphicsPixmapItem()
        self.lab_item.setOpacity(0.6)
        self.ovl_item.setOpacity(0.45)
        for it in (self.img_item, self.ovl_item, self.lab_item):
            self.scene_.addItem(it)
        self.labels = None            # QImage ARGB32
        self.tool, self.brush = 1, 4
        self._last, self._pan = None, None
        self.undo = []
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#111"))
        self.setMouseTracking(True)

    def set_image(self, gray8, lab):
        self.img_item.setPixmap(QPixmap.fromImage(_gray_qimage(gray8)))
        self.labels = labels_to_qimage(lab)
        self.lab_item.setPixmap(QPixmap.fromImage(self.labels))
        self.ovl_item.setPixmap(QPixmap())
        self.scene_.setSceneRect(QRectF(0, 0, gray8.shape[1], gray8.shape[0]))
        self.undo = []
        self.fit()

    def set_overlay(self, mask, color):
        if mask is None:
            self.ovl_item.setPixmap(QPixmap())
            return
        rgba = np.zeros(mask.shape + (4,), np.uint8)
        rgba[mask] = (color.red(), color.green(), color.blue(), 255)
        self.ovl_item.setPixmap(QPixmap.fromImage(_rgba_qimage(rgba)))

    def fit(self):
        self.resetTransform()
        self.fitInView(self.scene_.sceneRect(), Qt.KeepAspectRatio)

    def wheelEvent(self, e):
        f = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.scale(f, f)

    def _paint_to(self, p):
        if self.labels is None:
            return
        qp = QPainter(self.labels)
        qp.setRenderHint(QPainter.Antialiasing, False)
        if self.tool == 0:
            qp.setCompositionMode(QPainter.CompositionMode_Clear)
            col = QColor(0, 0, 0, 0)
        else:
            qp.setCompositionMode(QPainter.CompositionMode_Source)
            col = FIBER if self.tool == 1 else BACKGROUND
        pen = QPen(col, self.brush, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        qp.setPen(pen)
        a = self._last if self._last is not None else p
        if a == p:
            qp.drawPoint(p)
        else:
            qp.drawLine(a, p)
        qp.end()
        self._last = p
        self.lab_item.setPixmap(QPixmap.fromImage(self.labels))

    def mousePressEvent(self, e):
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan = e.position()
            return
        if e.button() == Qt.LeftButton and self.labels is not None:
            self.undo.append(self.labels.copy())
            del self.undo[:-20]
            self._last = None
            self._paint_to(self.mapToScene(e.position().toPoint()))

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if e.buttons() & Qt.LeftButton and self._last is not None:
            self._paint_to(self.mapToScene(e.position().toPoint()))

    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan = None
            return
        if e.button() == Qt.LeftButton and self._last is not None:
            self._last = None
            self.painted.emit()

    def undo_last(self):
        if self.undo:
            self.labels = self.undo.pop()
            self.lab_item.setPixmap(QPixmap.fromImage(self.labels))
            self.painted.emit()


class FiberModelPage(Page):
    model_saved = Signal(str)

    def __init__(self, data_page, params_page, parent=None):
        super().__init__("Fiber model", "Teach the app which pixels are fiber: paint a few strokes on fibers and on "
                         "background, train, check the result, correct where it is wrong, train again. The model "
                         "then replaces the ridge threshold (Settings step).", scroll=False)
        self.data_page, self.params_page = data_page, params_page
        self.ann = {}          # (path, t, z) -> dict(labels, px, name, flat (cached or None))
        self.cur = None        # current image dict
        self.model = None
        self._stack = (None, None)
        self._task = None
        try:
            import sklearn  # noqa: F401
            self.have_sklearn = True
        except ImportError:
            self.have_sklearn = False

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.lay.addWidget(split, 1)
        ctrl = QWidget()
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(0, 0, 8, 0)
        cl.setSpacing(10)

        g = QGroupBox("1. Image")
        gl = QVBoxLayout(g)
        self.file = QComboBox()
        self.file.setMinimumContentsLength(18)
        self.file.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        gl.addWidget(hbox(self.file, button("Browse…", "link", slot=self.browse), stretch_end=False))
        self.slice = QSpinBox()
        self.slice.setRange(0, 0)
        self.load_btn = button("Load image", "primary", slot=self.load_image)
        gl.addWidget(hbox(QLabel("Image no."), self.slice, self.load_btn))
        self.info = hint("Files come from the Files step (ticked files). Use several images: different samples, "
                         "depths, before and after AMF.")
        gl.addWidget(self.info)
        cl.addWidget(g)

        g = QGroupBox("2. Paint")
        gl = QVBoxLayout(g)
        self.tools = QButtonGroup(self)
        rb = []
        for i, (txt, key) in enumerate((("Eraser", "E"), ("Fiber", "F"), ("Background", "B"))):
            r = QRadioButton(f"{txt}  [{key}]")
            self.tools.addButton(r, i)
            rb.append(r)
        rb[1].setStyleSheet(f"color: {FIBER.name()}; font-weight: 700;")
        rb[2].setStyleSheet(f"color: {BACKGROUND.name()}; font-weight: 700;")
        rb[1].setChecked(True)
        self.tools.idToggled.connect(lambda i, on: on and setattr(self.canvas, "tool", i))
        gl.addWidget(hbox(rb[1], rb[2], rb[0]))
        self.brush = QSlider(Qt.Horizontal)
        self.brush.setRange(1, 40)
        self.brush.setValue(4)
        self.brush_lbl = QLabel("brush 4 px")
        self.brush.valueChanged.connect(self._brush)
        gl.addWidget(hbox(self.brush_lbl, self.brush, stretch_end=False))
        gl.addWidget(hbox(button("Undo  [Ctrl+Z]", "link", slot=lambda: self.canvas.undo_last()),
                          button("Clear this image", "link", slot=self.clear_image),
                          button("Fit view", "link", slot=lambda: self.canvas.fit())))
        gl.addWidget(hint("Thin strokes along the middle of clear fibers, and strokes in the gaps between them. "
                          "Paint the hard places too (faint fibers, bright blobs that are not fibers). Wheel = "
                          "zoom, right-drag = move."))
        self.ann_lbl = QLabel("No strokes yet.")
        self.ann_lbl.setWordWrap(True)
        gl.addWidget(self.ann_lbl)
        gl.addWidget(hbox(button("Save strokes…", "link", slot=self.save_strokes),
                          button("Load strokes…", "link", slot=self.load_strokes)))
        cl.addWidget(g)

        g = QGroupBox("3. Train and check")
        gl = QVBoxLayout(g)
        self.train_btn = button("Train on all painted images", "primary", slot=self.train)
        self.train_btn.setEnabled(self.have_sklearn)
        gl.addWidget(self.train_btn)
        self.show_grp = QButtonGroup(self)
        self.show_none = QRadioButton("strokes only")
        self.show_model = QRadioButton("model mask (green)")
        self.show_otsu = QRadioButton("ridge + Otsu mask (yellow), for comparison")
        for i, r in enumerate((self.show_none, self.show_model, self.show_otsu)):
            self.show_grp.addButton(r, i)
        self.show_none.setChecked(True)
        self.show_grp.idToggled.connect(lambda i, on: on and self.update_overlay())
        gl.addWidget(QLabel("Show on the image:"))
        for r in (self.show_none, self.show_model, self.show_otsu):
            gl.addWidget(r)
        self.cut = QSlider(Qt.Horizontal)
        self.cut.setRange(5, 95)
        self.cut.setValue(50)
        self.cut_lbl = QLabel("cutoff 0.50")
        self.cut.valueChanged.connect(self._cut)
        gl.addWidget(hbox(self.cut_lbl, self.cut, stretch_end=False))
        self.res = QLabel("Not trained yet.")
        self.res.setWordWrap(True)
        self.res.setTextFormat(Qt.RichText)
        gl.addWidget(self.res)
        self.save_btn = button("Save model and use it for new runs…", slot=self.save_model)
        self.save_btn.setEnabled(False)
        gl.addWidget(self.save_btn)
        gl.addWidget(hint("Check the model mask on images you did not paint before trusting it. Results with a "
                          "model are not directly comparable with ridge-threshold results: analyse all files you "
                          "compare with the same model."))
        if not self.have_sklearn:
            gl.addWidget(banner("scikit-learn is not installed (pip install scikit-learn): training is not "
                                "available."))
        cl.addWidget(g)
        cl.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(ctrl)
        sa.setMinimumWidth(470)
        split.addWidget(sa)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.canvas = Canvas()
        self.canvas.painted.connect(self._stroke_done)
        rl.addWidget(self.canvas, 1)
        self.status = hint("")
        rl.addWidget(self.status)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([460, 860])
        for key, fn in (("F", lambda: self.tools.button(1).setChecked(True)),
                        ("B", lambda: self.tools.button(2).setChecked(True)),
                        ("E", lambda: self.tools.button(0).setChecked(True)),
                        ("Ctrl+Z", lambda: self.canvas.undo_last()),
                        ("]", lambda: self.brush.setValue(self.brush.value() + 1)),
                        ("[", lambda: self.brush.setValue(self.brush.value() - 1))):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(fn)
        self.data_page.changed.connect(self.update_files)
        self.update_files()
        self._autoload()

    # ------------------------------------------------------------------ files / images
    def update_files(self):
        cur = self.file.currentData()
        self.file.blockSignals(True)
        self.file.clear()
        paths = list(self.data_page.selected_files())
        for p in {k[0] for k in self.ann} - set(paths):
            paths.append(p)
        for p in paths:
            n_ann = sum(1 for k in self.ann if k[0] == p)
            self.file.addItem(os.path.basename(p) + (f"   ✎ {n_ann}" if n_ann else ""), p)
        i = self.file.findData(cur)
        self.file.setCurrentIndex(max(i, 0))
        self.file.blockSignals(False)

    def browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Image file", "", "Images (*.nd2 *.tif *.tiff)")
        if p:
            if self.file.findData(p) < 0:
                self.file.addItem(os.path.basename(p), p)
            self.file.setCurrentIndex(self.file.findData(p))
            self.load_image()

    def _P(self):
        return self.params_page.namespace()

    def _load_job(self, path, idx, P):
        if self._stack[0] != path:
            s, px, dz, times = core.load_stack(path, P.pixel_size, P.z_step, P.stack_axis, P.t_interval)
            self._stack = (path, (s, px))
        s, px = self._stack[1]
        G = core.geometry(px, P)
        frames = core.frames_to_analyse(s.shape, 1, 1)
        idx = min(idx, len(frames) - 1)
        t, z = frames[idx]
        flat = core.flatten(s[t, z], G["sigma_bg"])
        lo, hi = np.percentile(flat, (1, 99.7))
        gray = np.clip((flat - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
        return dict(path=path, t=t, z=z, idx=idx, n=len(frames), px=px, flat=flat, gray=gray, G=G)

    def load_image(self):
        path = self.file.currentData()
        if not path:
            QMessageBox.information(self, "Fiber model", "Tick files in the Files step, or Browse for an image.")
            return
        self._store_current()
        self._run(Task(self._load_job, path, self.slice.value(), self._P()), self._loaded,
                  f"Loading {os.path.basename(path)} …")

    def _loaded(self, d):
        self.cur = d
        self.slice.setMaximum(d["n"] - 1)
        key = (d["path"], d["t"], d["z"])
        lab = self.ann[key]["labels"] if key in self.ann else np.zeros(d["flat"].shape, np.uint8)
        if lab.shape != d["flat"].shape:
            lab = np.zeros(d["flat"].shape, np.uint8)
        self.canvas.set_image(d["gray"], lab)
        self.info.setText(f"{os.path.basename(d['path'])}: image {d['idx']} of {d['n']} "
                          f"(t {d['t']}, z {d['z']}), pixel {d['px']:.4f} µm.")
        d["prob"] = d["otsu_mask"] = None
        self.update_overlay()

    def _run(self, task, ok, msg):
        if self._task is not None and self._task.isRunning():
            return
        self._task = task
        self.status.setText(msg)
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)

        def done(r):
            QGuiApplication.restoreOverrideCursor()
            self.status.setText("")
            ok(r)

        def failed(m):
            QGuiApplication.restoreOverrideCursor()
            self.status.setText("")
            QMessageBox.warning(self, "Fiber model", m)
        task.ok.connect(done)
        task.failed.connect(failed)
        task.start()

    # ------------------------------------------------------------------ strokes
    def _brush(self, v):
        self.canvas.brush = v
        self.brush_lbl.setText(f"brush {v} px")

    def _store_current(self):
        if self.cur is None or self.canvas.labels is None:
            return
        lab = qimage_to_labels(self.canvas.labels)
        key = (self.cur["path"], self.cur["t"], self.cur["z"])
        if lab.any():
            self.ann[key] = dict(labels=lab, px=self.cur["px"], flat=self.cur["flat"])
        else:
            self.ann.pop(key, None)

    def _stroke_done(self):
        self._store_current()
        self._count()

    def clear_image(self):
        if self.cur is None:
            return
        self.canvas.undo.append(self.canvas.labels.copy())
        self.canvas.labels.fill(Qt.transparent)
        self.canvas.lab_item.setPixmap(QPixmap.fromImage(self.canvas.labels))
        self._stroke_done()

    def _count(self):
        nf = sum(int((a["labels"] == 1).sum()) for a in self.ann.values())
        nb = sum(int((a["labels"] == 2).sum()) for a in self.ann.values())
        files = len({k[0] for k in self.ann})
        self.ann_lbl.setText(f"Painted: {len(self.ann)} image(s) from {files} file(s); {nf} fiber and {nb} "
                             f"background pixels." if self.ann else "No strokes yet.")
        self._autosave()

    def _strokes_path(self):
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~")
        os.makedirs(os.path.join(base, "models"), exist_ok=True)
        return os.path.join(base, "models", "fiber_strokes.npz")

    def _write_strokes(self, path):
        arrs = {}
        for i, ((p, t, z), a) in enumerate(self.ann.items()):
            arrs[f"lab{i}"] = a["labels"]
            arrs[f"meta{i}"] = np.array([p, str(t), str(z), str(a["px"])])
        np.savez_compressed(path, **arrs)

    def _read_strokes(self, path):
        f = np.load(path, allow_pickle=False)
        n = 0
        for k in f.files:
            if k.startswith("lab"):
                i = k[3:]
                p, t, z, px = f[f"meta{i}"]
                self.ann[(str(p), int(t), int(z))] = dict(labels=f[k], px=float(px), flat=None)
                n += 1
        return n

    def _autosave(self):
        try:
            self._write_strokes(self._strokes_path())
        except Exception:
            pass

    def _autoload(self):
        try:
            if os.path.exists(self._strokes_path()):
                self._read_strokes(self._strokes_path())
                self._count()
                self.update_files()
        except Exception:
            pass

    def save_strokes(self):
        self._store_current()
        p, _ = QFileDialog.getSaveFileName(self, "Save strokes", "fiber_strokes.npz", "Strokes (*.npz)")
        if p:
            self._write_strokes(p)

    def load_strokes(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load strokes", "", "Strokes (*.npz)")
        if p:
            try:
                n = self._read_strokes(p)
            except Exception as e:
                QMessageBox.warning(self, "Load strokes", str(e))
                return
            self._count()
            self.update_files()
            self.status.setText(f"{n} painted image(s) loaded.")

    # ------------------------------------------------------------------ training / checking
    def _train_job(self, items, P):
        samples = []
        missing = []
        for (path, t, z), a in items:
            flat = a.get("flat")
            if flat is None:
                if not os.path.exists(path):
                    missing.append(os.path.basename(path))
                    continue
                s, px, dz, _ = core.load_stack(path, P.pixel_size, P.z_step, P.stack_axis, P.t_interval)
                G = core.geometry(px, P)
                flat = core.flatten(s[t, z], G["sigma_bg"])
                a["flat"] = flat
            samples.append((flat, a["px"], a["labels"]))
        m = FM.train(samples, sigma_bg_um=P.sigma_bg_um)
        return m, missing

    def train(self):
        self._store_current()
        if not self.ann:
            QMessageBox.information(self, "Fiber model", "Paint some fiber and background strokes first.")
            return
        self._run(Task(self._train_job, list(self.ann.items()), self._P()), self._trained,
                  "Training … (a few seconds per painted image)")

    def _trained(self, r):
        m, missing = r
        self.model = m
        top = ", ".join(f"{k} ({v:.0%})" for k, v in m["importances"][:4])
        self.res.setText(f"<b>Trained</b> on {m['n_images']} image(s): {m['n_fiber_px']} fiber and "
                         f"{m['n_background_px']} background pixels. Out-of-bag accuracy on the painted pixels: "
                         f"<b>{m['oob_accuracy']:.1%}</b> (painted pixels are the easy ones — judge the mask on the "
                         f"image).<br>Most used filters: {top}."
                         + (f"<br>Not found (skipped): {', '.join(missing)}" if missing else ""))
        self.save_btn.setEnabled(True)
        if self.cur is not None:
            self.cur["prob"] = None
            self.show_model.setChecked(True)
            self.update_overlay()

    def _cut(self, v):
        self.cut_lbl.setText(f"cutoff {v / 100:.2f}")
        if self.show_model.isChecked():
            self.update_overlay()

    def update_overlay(self):
        d = self.cur
        if d is None:
            return
        mode = self.show_grp.checkedId()
        if mode == 0:
            self.canvas.set_overlay(None, None)
            return
        if mode == 1:
            if self.model is None:
                self.status.setText("Train a model first.")
                self.canvas.set_overlay(None, None)
                return
            if d.get("prob") is None:
                self._run(Task(FM.probability, d["flat"], d["px"], self.model),
                          lambda p: (d.__setitem__("prob", p), self.update_overlay()), "Predicting …")
                return
            mask = core.remove_small(d["prob"] > self.cut.value() / 100, d["G"]["min_obj_px"])
            self.canvas.set_overlay(mask, QColor(40, 220, 90))
            self.status.setText(f"Model mask: {100 * mask.mean():.1f} % fiber at cutoff {self.cut.value() / 100:.2f}.")
        else:
            if d.get("otsu_mask") is None:
                rdg = core.ridge(d["flat"], d["G"]["ridge_sigmas"])
                d["otsu_mask"] = core.remove_small(rdg > core.otsu_threshold(rdg.ravel()), d["G"]["min_obj_px"])
            self.canvas.set_overlay(d["otsu_mask"], QColor(255, 220, 0))
            self.status.setText(f"Ridge + Otsu mask of this image: {100 * d['otsu_mask'].mean():.1f} % fiber.")

    def save_model(self):
        if not self.model:
            return
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~")
        start = QSettings().value("fibermodel/last_path", "") or os.path.join(base, "models", "fiber_model.joblib")
        os.makedirs(os.path.dirname(start), exist_ok=True)
        p, _ = QFileDialog.getSaveFileName(self, "Save fiber model", start, "Fiber model (*.joblib)")
        if not p:
            return
        if not p.endswith(".joblib"):
            p += ".joblib"
        try:
            FM.save_model(p, self.model)
        except Exception as e:
            QMessageBox.warning(self, "Save model", f"Could not save the model:\n{e}")
            return
        FM._CACHE.clear()
        QSettings().setValue("fibermodel/last_path", p)
        self.params_page.fm_cut.setValue(self.cut.value() / 100)
        self.model_saved.emit(p)
        QMessageBox.information(self, "Model saved", f"Saved to\n{p}\n\nThe Settings step now uses it (Fiber "
                                "threshold → Trained fiber model, cutoff {:.2f}). Switch back to Otsu there at any "
                                "time.".format(self.cut.value() / 100))
