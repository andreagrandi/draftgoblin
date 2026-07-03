# Pick scoring

Draftgoblin turns each offered card into one integer score from 0 to 100.

The base rating is 17Lands GIH WR when the card has enough games-in-hand samples. If QuickDraft data is missing or thin, the resolver falls back to PremierDraft. If neither format has a strong GIH sample, the card uses a neutral prior, adjusted by ALSA when ALSA is available: earlier ALSA raises the prior, later ALSA lowers it.

Scores are normalized against the set rating distribution and centered so the neutral prior displays as 50. Color commitment is intentionally a multiplier of 1.0 for this milestone; later color logic can change the adjusted rating before normalization.

Displayed scores are whole-number integers. We do not show one decimal for ties because the plain draft table should stay easy to scan; ties are resolved deterministically by raw score, base rating, and original pack order.

Rows marked `Prior*` did not have a strong GIH sample. The `Source` column shows whether a row used QuickDraft, Premier fallback, or the prior.
