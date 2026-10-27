# Black Box Terminal — design study 03

Local static prototype. Open `preview.html` for the original centered Desktop/Phone comparison; `index.html` uses the available viewport. The preserved first study is under `v1/`. No build, account or remote service is required. Three fonts are locally bundled with licenses and source hashes in `fonts/`.

## Direction and behaviour

One continuous near-black background, with the previous centered content dimensions and side space preserved. The preview frame and review bar are transparent; lighting does not restart at the content edges. Outfit is the default typeface. Manrope and Space Grotesk remain in a temporary type-comparison control. Newspaper influence comes from columns, datelines and hierarchy; subtle rounded surfaces and a soft reader transition remain.

Category columns, one selected category on phone, chronology by capture date, search, sample date ranges, source links and keyboard/browser-Back article navigation remain. Same 20 sample records; display categories remain provisional.

## Scrolling — handoff accepted, gentler feel awaiting review

Study 02 fixed the column handoff, confirmed by hand with a laptop trackpad. Study 03 preserves that routing and uses 60% of wheel travel with short, independent easing per column. Reversal and input/view interruption cancel pending movement; boundaries cannot bank overscroll. Reduced motion uses the scaled travel immediately. Touch, keyboard, chronology, reader scrolling and pinch zoom remain native.

Routing uses a browser momentum flag when available; otherwise timing, reversal and increased impulse identify fresh input. This fallback remains heuristic. Seventeen deterministic tests pass; subjective speed/stopping still needs a hands-on review.

```sh
node --test design-prototype/scroll-controller.test.cjs
```

Run from the project root. Retest by scrolling A, moving to B while A settles, then starting a fresh scroll over B; also check reversal and the overall pace.

## Content boundary

Twenty records from the September test archive. Headlines/summaries treat captured material; they are not independently verified publication-ready reporting. Dates are capture dates, which can differ from event dates. Actual source provenance remains visible. No collection, human-label edits, source enrichment, Telegram delivery, publication or automatic refresh is connected.

## Open locally

Open `preview.html`, or serve only this directory:

```sh
python3 -m http.server 8767 --bind 127.0.0.1 --directory design-prototype
```

Run it from the project root, then open `http://127.0.0.1:8767/preview.html`. Direct files also work. Preview controls are design tools, not public product features.

## Evidence

Screenshots and check notes are kept privately, outside this repository. Browser viewport simulations are not physical phone tests. The first study is preserved in `v1/`.
