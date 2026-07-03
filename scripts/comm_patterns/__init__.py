"""comm_patterns — extractor, classifier, scrubber, store.

Slice 2 of #526 / #581. Writes one row per detected communication pattern
into the `comm_patterns` Supabase table. ADR 0004 locks the schema and
column choices; this package only handles the extraction pipeline.
"""
