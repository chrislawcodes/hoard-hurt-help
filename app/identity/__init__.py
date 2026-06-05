"""Identity rules shared across public-facing text.

`word_filter` holds the one shared bad-words / reserved list; `handle` builds the
public operator handle on top of it. Kept in its own package so the rules have a
single home rather than leaking into route modules.
"""
