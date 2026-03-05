# Search Pipeline Mapping (src -> main core)

## Goal
Transfer only job-search scraping logic from root `/src` into the current Resume Matcher core, without importing Ollama-based profile/augment features.

## Source analyzed
- `src/app/scrape/route.ts`
- `src/lib/scrapers/fetchWithTimeout.ts`
- `src/lib/scrapers/types.ts`
- `src/lib/scrapers/nofluffjobs.ts`
- `src/lib/scrapers/justjoinit.ts`
- `src/lib/scrapers/bulldogjob.ts`
- `src/lib/scrapers/theprotocol.ts`
- `src/lib/scrapers/solidjobs.ts`
- `src/app/page.tsx` (query/stream contract only)

## What was transferred
### Backend API
- New endpoint: `GET /api/v1/search/scrape`
- Supports:
  - regular JSON mode
  - SSE mode (`stream=1`) with `progress`, `done`, `error` events

### Query behavior (ported)
- `limit`
- `keywords` and alias `q`
- keyword mode aliases: `keywordMode`, `keywordsMode`, `matchMode`
- salary-range filter aliases: `salaryRangeOnly`, `withSalaryRange`, `salaryOnly`
- sort aliases:
  - `sortBy`, `sort`, `orderBy`
  - `sortDirection`, `sortOrder`, `order`
- stream aliases: `stream`, `progress`
- per-source limit aliases:
  - NoFluffJobs: `scrapeLimitNoFluffJobs`, `scrapeLimitNoFluff`, `nofluffjobsLimit`, `nofluffLimit`
  - JustJoinIT: `scrapeLimitJustJoinIt`, `scrapeLimitJustJoin`, `justjoinitLimit`, `jjiLimit`
  - Bulldogjob: `scrapeLimitBulldogJob`, `scrapeLimitBulldogjob`, `bulldogjobLimit`, `bulldogLimit`
  - theprotocol.it: `scrapeLimitTheProtocol`, `scrapeLimitTheProtocolIt`, `theprotocolLimit`, `protocolLimit`
  - Solid.jobs: `scrapeLimitSolidJobs`, `scrapeLimitSolid`, `solidjobsLimit`, `solidLimit`

### Pipeline behavior (ported)
- parallel source scraping
- per-source timeout
- retries on `429` in HTTP helper
- URL/id deduplication
- keyword filtering (`AND`/`OR`)
- salary-range-only filtering
- sorting by relevance/name/salary
- partial-failure tolerance (`errors` array)
- `502` when all attempted active sources fail
- telemetry in `meta`:
  - duration
  - requested/actual per source
  - scraped/returned counts
  - deduped count

### Providers transferred
- NoFluffJobs
- JustJoinIT
- Bulldogjob
- theprotocol.it
- Solid.jobs

### Frontend
- `apps/frontend/app/(default)/search/page.tsx` now runs real search against backend.
- Uses SSE progress updates and renders results/errors/metadata.

## Intentionally NOT transferred
- `/profile` builder logic
- `/augment-cv` logic
- any Ollama/LLM extraction/tailoring integration from `/src`
- local storage profile/augment persistence from `/src`

