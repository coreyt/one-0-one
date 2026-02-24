"""Agent color palette — 6 colors cycled by agent index."""

AGENT_PALETTE = [
    "#4fc3f7",  # 0 — light blue
    "#81c784",  # 1 — green
    "#ffb74d",  # 2 — orange
    "#f06292",  # 3 — pink
    "#ba68c8",  # 4 — purple
    "#4db6ac",  # 5 — teal
]


def agent_color(index: int) -> str:
    return AGENT_PALETTE[index % len(AGENT_PALETTE)]
