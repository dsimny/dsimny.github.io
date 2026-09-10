# Vendored fonts

These files exist for one reason: **`scripts/render_recap_image.py` must produce
byte-identical output on every machine.** A font resolved by *name* is resolved
against whatever the host happens to have installed, so the same ledger would
render differently on Daniel's Windows box and on an Ubuntu runner, and would
change silently whenever GitHub refreshes the runner image. The renderer
therefore loads these files by **repository-relative path only** and never asks
the system for a font.

Pillow no longer ships a font of its own (verified with Pillow 12.3.0:
`PIL/fonts/DejaVuSans.ttf` does not exist), so vendoring is not optional.

## What is here

| File | Purpose |
|---|---|
| `DejaVuSans.ttf` | Body text |
| `DejaVuSans-Bold.ttf` | Headline, day line, running-ledger figure, result words |
| `LICENSE-DejaVu.txt` | The upstream licence, verbatim and unmodified |

## Provenance

- **Family:** DejaVu Sans
- **Version:** 2.37 (the current release; the TTFs are dated 2016-07-30)
- **Source:** <https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip>
- **Archive SHA-256:** `7576310b219e04159d35ff61dd4a4ec4cdba4f35c00e002a136f00e96a908b0a`
- **Retrieved:** 2026-09-10

Extracted file checksums (SHA-256), asserted by `scripts/selftest_instagram.py`
so a swapped or corrupted font fails the suite rather than quietly changing
every card:

```
7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954  DejaVuSans.ttf
e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724  DejaVuSans-Bold.ttf
7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93  LICENSE-DejaVu.txt
```

## Licence

The DejaVu Fonts Licence (a Bitstream Vera derivative) — permissive, and it
explicitly permits redistribution, including bundled in a larger work. This
repository is public and serves GitHub Pages, so the fonts are redistributed
publicly; `LICENSE-DejaVu.txt` travels with them, which is what the licence
asks for. Keep the two together.

## Known lint exception

`git diff --check` reports exactly one problem in this directory, and it is
deliberate:

```
assets/fonts/LICENSE-DejaVu.txt:77: trailing whitespace.
+or Font Software that has been modified and is distributed under the
```

That single trailing space is present in the upstream DejaVu 2.37 release. It is
the only trailing-whitespace line in the file (the whole file was scanned, not
just the line git happened to report), and it is part of what the recorded
SHA-256 above hashes.

**Do not strip it.** Editing the text would alter a licence document we
redistribute verbatim, and it would break `selftest_instagram.py`'s checksum
check, which is the guard that a font or its licence has not been swapped.
`.gitattributes` is deliberately left alone as well — a repo-wide
`-whitespace` rule would suppress genuine findings in real source files to
silence one line we do not own.

Nothing in CI runs `git diff --check`, so this gates nothing. It is recorded
here so the next person to see the warning knows it is expected rather than
rediscovering it. Every file authored in this repository carries zero trailing
whitespace; this one imported file is the sole exception.

## Why DejaVu rather than a closer match to the site's `system-ui`

Determinism outranks typographic fashion here, and DejaVu is the most
thoroughly exercised TTF in the Pillow ecosystem. Swapping to Inter (SIL OFL
1.1) later is a two-line change plus new checksums, because the renderer takes
a path — nothing else in the codebase knows the family name.

## Do not typeset result marks in this font

Verified against `DejaVuSans.ttf` at 40 px:

| Codepoint | Result |
|---|---|
| U+2705 ✅ | renders `.notdef` — a tofu box |
| U+274C ❌ | renders `.notdef` — the **same** tofu box |
| U+26AA ⚪ | renders a real glyph |

So a win and a loss would be visually identical while a void looked correct —
a failure that reads as "working" at a glance. `render_recap_image.py` draws
the WIN / LOSS / VOID marks as filled circles with stroked primitives and sets
the literal word beside every one, so the result never depends on a glyph and
never depends on colour alone.
