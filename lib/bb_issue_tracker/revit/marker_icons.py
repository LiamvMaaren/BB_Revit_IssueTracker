# -*- coding: utf-8 -*-
"""Generate fixed-pixel circular BMP markers for Revit in-canvas controls.

Revit's InCanvasControlData accepts absolute BMP paths.  Icons are rendered
lazily on the Revit UI thread and cached in the user's temporary directory.
"""

import os
import re

from System.Globalization import CultureInfo
from System.IO import FileAccess, FileMode, FileShare, FileStream
from System.Windows import FlowDirection, Point, Rect
from System.Windows.Media import (
    BrushConverter, DrawingVisual, Pen, PixelFormats, Typeface
)
from System.Windows.Media.Imaging import (
    BitmapFrame, BmpBitmapEncoder, FormatConvertedBitmap, RenderTargetBitmap
)
from System.Windows.Media import FormattedText

from bb_issue_tracker.status import palette_for
from bb_issue_tracker.textutils import to_text


TRANSPARENT_KEY = "#008080"
WIDTH = 48
HEIGHT = 48


def short_issue_number(issue_id):
    text = to_text(issue_id).strip()
    match = re.search(r"#(\d+)$", text)
    if match:
        return match.group(1)
    matches = re.findall(r"\d+", text)
    return matches[-1] if matches else "?"


def _safe_token(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", to_text(value)) or "marker"


class MarkerIconCache(object):
    def __init__(self, cache_dir, fallback_dir=""):
        self.cache_dir = os.path.abspath(cache_dir)
        self.fallback_dir = os.path.abspath(fallback_dir) if fallback_dir else ""
        if not os.path.isdir(self.cache_dir):
            os.makedirs(self.cache_dir)

    def icon_path(self, issue_id, status):
        number = short_issue_number(issue_id)
        filename = "pin_{0}_{1}.bmp".format(
            _safe_token(status).lower(), _safe_token(number)
        )
        path = os.path.join(self.cache_dir, filename)
        if os.path.isfile(path):
            return path
        try:
            self._render(path, number, status)
            return path
        except Exception:
            fallback = os.path.join(
                self.fallback_dir,
                "pin_{0}.bmp".format(_safe_token(status).lower())
            )
            if self.fallback_dir and os.path.isfile(fallback):
                return fallback
            raise

    def _render(self, path, number, status):
        converter = BrushConverter()
        background = converter.ConvertFromString(TRANSPARENT_KEY)
        fill = converter.ConvertFromString(palette_for(status).get("marker"))
        white = converter.ConvertFromString("#FFFFFF")

        visual = DrawingVisual()
        drawing = visual.RenderOpen()
        try:
            drawing.DrawRectangle(background, None, Rect(0, 0, WIDTH, HEIGHT))

            outline = Pen(white, 3.0)
            outline.Freeze()
            drawing.DrawEllipse(
                fill,
                outline,
                Point(WIDTH / 2.0, HEIGHT / 2.0),
                20.0,
                20.0
            )

            text = to_text(number)
            if len(text) <= 2:
                font_size = 16.0
            elif len(text) == 3:
                font_size = 14.0
            elif len(text) == 4:
                font_size = 12.0
            else:
                font_size = 10.0
            typeface = Typeface("Segoe UI Semibold")
            try:
                formatted = FormattedText(
                    text,
                    CultureInfo.GetCultureInfo("nl-NL"),
                    FlowDirection.LeftToRight,
                    typeface,
                    font_size,
                    white,
                    1.0
                )
            except TypeError:
                formatted = FormattedText(
                    text,
                    CultureInfo.GetCultureInfo("nl-NL"),
                    FlowDirection.LeftToRight,
                    typeface,
                    font_size,
                    white
                )
            drawing.DrawText(
                formatted,
                Point(
                    (WIDTH - formatted.Width) / 2.0,
                    (HEIGHT - formatted.Height) / 2.0
                )
            )
        finally:
            drawing.Close()

        bitmap = RenderTargetBitmap(WIDTH, HEIGHT, 96.0, 96.0, PixelFormats.Pbgra32)
        bitmap.Render(visual)

        # Revit is most reliable with classic uncompressed 24-bit BMP files.
        # WPF otherwise writes the Pbgra32 render target as a 32-bit bitmap,
        # which some Revit/Windows combinations reject in AddControl.
        converted = FormatConvertedBitmap()
        converted.BeginInit()
        converted.Source = bitmap
        converted.DestinationFormat = PixelFormats.Bgr24
        converted.EndInit()
        converted.Freeze()

        encoder = BmpBitmapEncoder()
        encoder.Frames.Add(BitmapFrame.Create(converted))
        stream = FileStream(path, FileMode.Create, FileAccess.Write, getattr(FileShare, "None"))
        try:
            encoder.Save(stream)
        finally:
            stream.Close()
