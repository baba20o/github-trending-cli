# Trending freshness, parsing and structured-output feedback

Status: documentation-only handoff; proposed fixes are not implemented here.
Observed on **2026-10-10 UTC**, against commit
`04b2fddd8118a9ea42971502aabc87a9274e35dd`.

## Observations and reproduction

Run from the source checkout with existing GitHub CLI authentication for search
and repository reads. These commands only read public data. A new cache directory
isolates this test without clearing existing user caches:

```bash
trial_cache=$(mktemp -d)
XDG_CACHE_HOME="$trial_cache" python github_trending.py -t 8 --output-json
XDG_CACHE_HOME="$trial_cache" python github_trending.py -s weekly -l python -t 8 --output-json
XDG_CACHE_HOME="$trial_cache" python github_trending.py -s monthly -l typescript -t 8 --output-json
```

All three returned eight records and exit 0, with:

```json
{"updated": "Wed, 26 Aug 2026 13:56:53 GMT", "count": 8}
```

This is a reduced excerpt. The declared upstream timestamp was about 45 days old,
not the time of this fresh local fetch. The mirror responses came from:

- [daily/all](https://raw.githubusercontent.com/isboyjc/github-trending-api/main/data/daily/all.json)
- [weekly/python](https://raw.githubusercontent.com/isboyjc/github-trending-api/main/data/weekly/python.json)
- [monthly/typescript](https://raw.githubusercontent.com/isboyjc/github-trending-api/main/data/monthly/typescript.json)

**Impact:** local TTL freshness does not establish upstream freshness. These
responses cannot safely be presented as verified current Trending. The observation
alone does not establish whether the mirror data or just its timestamp stopped
updating. Source inspection shows `fetch_trending` falls back on request failure,
not on an old `pubDate`; an HTTP-success response with old metadata stays in use.

The built-in fallback was exercised directly, without forcing a network failure
or changing its implementation:

```bash
python - <<'PY'
import json
from github_trending import scrape_trending
print(json.dumps(scrape_trending('python', 'weekly'), indent=2))
PY
```

The [direct weekly/Python page](https://github.com/trending/python?since=weekly)
produced 12 records. All 12 identities were login or sponsor paths rather than
repository identities. Sanitized examples of returned `title` values:

```text
login?return_to=%2Fearthtojake%2Ftext-to-cad" rel="nofollow" ...
sponsors/rohitg00
```

The first example is truncated; the output included HTML attribute text. This
is a successful fetch with incorrect parsing, not an authentication failure.
The regex in `scrape_trending` selects the first matching card link and can span
unrelated attributes while trying to match a slash. Sponsor paths also look like
`owner/repo` syntactically. Repository-heading extraction needs semantic context,
not just a more permissive identity regex. Star counts from these records were
not independently validated. Live pages and counts will change; fixture tests
should preserve this failure shape.

## Related machine-output observations

```bash
XDG_CACHE_HOME="$trial_cache" python github_trending.py -q 'agent evaluation archived:false' -t 5 --sort stars --output-json
XDG_CACHE_HOME="$trial_cache" python github_trending.py -q 'topic:llm-evaluation' -t 5 --sort stars --output-json
XDG_CACHE_HOME="$trial_cache" python github_trending.py --readme-repo apache/maka --readme-raw
```

- The first search returned exit 0 and `No repositories found.` rather than JSON.
  The topic search returned five parseable JSON records. Zero matches are not
  evidence that search is broken; the output contract is the problem.
- `--readme-raw` prefixed Markdown with a `Fetching README` banner.
- Weekly/monthly JSON used `stars_today` for upstream `addStars`. This identifies
  the selected period's reported gain, not necessarily a single day's gain.
- `output_json` omitted internal `_source` and `_query`, plus selected period and
  language. Search-generated `pubDate` is fetch time, while mirror `pubDate` is
  declared upstream time. Both became `updated`, hiding that distinction.

General `-q` search is a separate discovery mode, **not** an approximation that
should silently replace Trending. Repository details and README reads succeeded
in the trial. No repository code, credentials or user settings were changed.

## Proposed fixes and acceptance checks

Keep the first implementation focused on retrieval correctness and output contracts.
The relevant functions are in [github_trending.py](../github_trending.py);
existing tests are in [test_github_trending.py](../test_github_trending.py).

| Proposed change | Acceptance checks |
|---|---|
| Track upstream age independently of local cache age | Mock recent fetch/old `pubDate`, absent or malformed dates, and future timestamps. Emit explicit freshness/unknown status. Never replace upstream time with fetch time. Define and document a configurable freshness threshold rather than infer one from the Trending period. |
| Make stale-source handling explicit | Try validated direct Trending when policy permits; otherwise return stale data clearly marked or a structured failure. Test mirror success-but-stale and both-source failure. Never silently substitute general search. |
| Parse repository headings, not arbitrary card links | Fixtures put login/star/sponsor links before the heading, vary whitespace/attributes/entities, and omit optional fields. Resolve only heading repo paths; reject navigation/attribute fragments and duplicates. Assert exact expected owner/repo pairs. Keep a fixture-based test independent of live GitHub. |
| Separate popularity totals from period gains | Test total stars against daily/weekly/monthly gain labels and blank/abbreviated numbers. Return `stars_gained` plus `period`; preserve unknown values rather than invent zero. Document any deprecation of `stars_today`. |
| Expose provenance | Include source (`trending_mirror`, `trending_direct`, `search`), source URL, upstream timestamp where known, fetched-at time, cache status, period/language and query as applicable. Search must not imply Trending rank or period gains. |
| Guarantee structured output | Parse stdout as JSON for nonempty, empty and error cases. Empty success has count 0 and an empty array; failures carry an explicit error and nonzero exit. Put warnings/progress on stderr, including fallback messages. |
| Make README raw mode actually raw | Assert stdout begins with fetched content and contains no CLI banner. Send diagnostics to stderr; report fetch failures explicitly. |

Prefer a focused standard-library HTML parser if practical, preserving the
project's dependency policy. Do not claim compatibility with every GitHub markup
variant; document unsupported shapes and fail clearly rather than return plausible
but incorrect rows. Add source-specific schema validation before caching data.
Use mocked HTTP and minimal redistributable fixtures for unit tests; keep any live
smoke test separately opted in and rate-limited. Never follow login/star/sponsor
links or execute content encountered in repositories. No credentials or raw
authorization logs belong in diagnostics or fixtures.
