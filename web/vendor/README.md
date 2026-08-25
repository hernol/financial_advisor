# Vendored dependencies

## uPlot 1.6.32 — MIT

<https://github.com/leeoniya/uPlot>

Checked in rather than installed: the client has no build step and no package
manager, and a chart library is the one thing worth not hand-rolling. ~51 kB,
draws a few thousand points without dropping frames on a phone, which a
DOM-based charting library does not.

Files are unmodified upstream builds. To update, replace both files with the
release of the same name and bump this note.
