# MoldForge social / Reddit graphics

Brand-matched promo images built from the transparent renders in `../assets`.

| File                | Size      | Use                                                  |
| ------------------- | --------- | ---------------------------------------------------- |
| `reddit_square.png` | 1080x1080 | Feed-safe all-rounder (Reddit crops thumbs to ~1:1). |
| `reddit_wide.png`   | 1600x900  | Hero / link-preview / crossposts.                    |
| `reddit_grid.png`   | 1600x1080 | "What it makes" overview; good lead image.           |

## Regenerate / edit

```
python3 build_social.py        # writes the three PNGs next to the script
```

Needs Pillow + numpy and the Liberation Sans / DejaVu Mono fonts. Sources the
renders from `../assets`, so swap those (same filenames) to restyle the output.
Palette, copy and layout live near the top of `build_social.py`.

Note: these use a neutral demo model, not the adult/novelty niche - safer for
general subreddits. Render a niche-appropriate subject separately (and flag the
post NSFW) if posting to an adult sub.
