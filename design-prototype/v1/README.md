# Black Box Terminal — first design prototype

Local, dependency-free design study. Open `preview.html` for a desktop/phone switcher, or `index.html` directly. No build, account, network service, analytics or external font required.

Implemented: category columns; one selected category on narrow screens; chronology grouped by capture date; text search; three sample date ranges; article reader with keyboard/back-button navigation; source links; gentle native scroll snapping; reduced-motion support.

Twenty selected records from the existing September test archive. Headlines and summaries are editorial treatments of that captured material, **not independently verified publication-ready reporting**. Dates displayed are capture dates; a roundup capture date is not necessarily the date of its underlying event. Source links retain their actual provenance. No new fact-checking or live ingestion performed.

The four column labels are provisional display groupings, not a change to the watcher's decision framework. The prototype has no connection to the collector, review decisions, Telegram delivery or publishing pipeline. There is no automatic refresh or hosting deployment.

Serve locally from the project root if desired:

```sh
python3 -m http.server 8767 --bind 127.0.0.1 --directory design-prototype
```

Then open http://127.0.0.1:8767/preview.html.
