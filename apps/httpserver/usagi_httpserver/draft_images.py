"""Parse the image-selection marker the WeChat material prompt emits."""

import re

_MARKER_LINE = re.compile(r"^\s*配图\s*[:：]\s*(.*)$")
_INDEX = re.compile(r"第?\s*(\d+)\s*张?")


def selected_image_indices(text, *, total):
    """Extract 1-based image indices from the last 配图 line of a draft.

    Returns None when no marker line exists (the caller decides the
    fallback), otherwise the deduplicated indices in order of appearance,
    filtered to the range 1..total — an explicit "配图：无" or a fully
    out-of-range selection yields an empty list meaning "no images".
    """
    marker = None
    for line in str(text).splitlines():
        match = _MARKER_LINE.match(line)
        if match:
            marker = match.group(1)
    if marker is None:
        return None
    indices = []
    for value in _INDEX.findall(marker):
        index = int(value)
        if 1 <= index <= total and index not in indices:
            indices.append(index)
    return indices
