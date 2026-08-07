# YouTube Transcript Edge Function — Testing

## Prerequisites

Install Deno and Supabase CLI:
```bash
# Deno (macOS/Linux)
curl -fsSL https://deno.land/install.sh | sh

# Supabase CLI (macOS/Linux)
brew install supabase/tap/supabase
```

## Unit Tests

```bash
cd supabase/functions/youtube-transcript

# Run all tests
deno test tests/

# Run specific test
deno test tests/grouping_test.ts
deno test tests/youtube_url_test.ts
deno test tests/contract_test.ts

# Watch mode
deno test --watch tests/
```

## Type Check

```bash
deno check index.ts youtube.ts grouping.ts errors.ts
```

## Local Development

```bash
cd /path/to/GrapplingArcAnalytics

# Start Supabase locally (includes function server)
supabase start

# In another terminal, serve the function
supabase functions serve youtube-transcript

# Function available at: http://localhost:54321/functions/v1/youtube-transcript
```

## Manual Testing with curl

### Success case (video with captions)
```bash
curl -X POST http://localhost:54321/functions/v1/youtube-transcript \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
```

### Invalid URL
```bash
curl -X POST http://localhost:54321/functions/v1/youtube-transcript \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

### Test from browser

Open `site/study.html` with config pointing to localhost:
```javascript
window.GA_CONFIG = {
  transcriptEndpoint: 'http://localhost:54321/functions/v1/youtube-transcript'
};
```

Then paste a YouTube URL and click Analyze.

## Integration with Site

When deployed, update `site/config.js`:
```javascript
window.GA_CONFIG = {
  transcriptEndpoint: 'https://<project-ref>.supabase.co/functions/v1/youtube-transcript'
};
```

## Common Issues

| Issue | Solution |
|-------|----------|
| "Module not found" | Run `deno check` first; missing imports will show |
| Port 54321 in use | Kill existing `supabase` process or use different port |
| CORS error in browser | Verify `ALLOWED_ORIGINS` in index.ts includes your origin |
| "No captions" | Video genuinely has no captions; try another video |
| Timeout (20s) | YouTube may be rate-limiting; try different video or wait |

## Deployment

When ready for production:
```bash
supabase functions deploy youtube-transcript \
  --project-ref <your-project-ref> \
  --auth-token <your-token>
```

Then update endpoint in `site/config.js` to prod URL.
