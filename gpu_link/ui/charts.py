"""Lightweight real-time plots; unknown samples remain gaps."""

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class Trace(QWidget):
    def __init__(self, title, unit, color, ceiling=None):
        super().__init__()
        self.title, self.unit, self.color, self.ceiling = title, unit, color, ceiling
        self.samples = deque(maxlen=300)
        self.setMinimumHeight(125)

    def add(self, value):
        self.samples.append(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#111d30"))
        p.setPen(QColor("#b8c9df"))
        current = self.samples[-1] if self.samples else None
        label = "Unavailable" if current is None else f"{current:.1f} {self.unit}"
        p.drawText(14, 23, f"{self.title}    {label}")
        values = [v for v in self.samples if v is not None]
        ceiling = self.ceiling or max(values + [1]) * 1.15
        p.setPen(QPen(QColor("#26354c"), 1, Qt.PenStyle.DotLine))
        p.drawLine(12, self.height() - 18, self.width() - 12, self.height() - 18)
        p.setPen(QPen(QColor(self.color), 2))
        previous = None
        for index, value in enumerate(self.samples):
            if value is None:
                previous = None
                continue
            point = QPointF(12 + index / 299 * (self.width() - 24),
                            self.height() - 18 - min(value / ceiling, 1) * (self.height() - 55))
            if previous is not None:
                p.drawLine(previous, point)
            previous = point
