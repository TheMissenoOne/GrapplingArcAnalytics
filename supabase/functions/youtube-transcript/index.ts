import { serve } from 'https://deno.land/std@0.208.0/http/server.ts';
import { TranscriptError } from './errors.ts';
import { groupTranscript } from './grouping.ts';
import { extractVideoId, fetchMetadata, captionsTimed } from './youtube.ts';

interface TranscriptRequest {
  url: string;
  languages?: string[];
}

interface TranscriptResponse {
  video: {
    id: string;
    url: string;
    title: string;
    channel: string;
    thumbnail: string;
    duration: number | null;
  };
  language: string;
  languageCode: string;
  generated: boolean;
  snippets: Array<{
    id: string;
    text: string;
  }>;
  segments: Array<{
    id: string;
    text: string;
    start: number;
    end: number;
  }>;
}

const ALLOWED_ORIGINS = [
  'https://themissenoone.github.io',
  'http://localhost:4000',
  'http://127.0.0.1:4000',
];

function validateRequest(body: unknown): TranscriptRequest {
  if (typeof body !== 'object' || body === null) {
    throw new TranscriptError('INVALID_YOUTUBE_URL');
  }

  const req = body as Record<string, unknown>;
  const url = String(req.url ?? '');

  if (!url || url.length > 2048 || !url.toLowerCase().includes('youtube')) {
    throw new TranscriptError('INVALID_YOUTUBE_URL');
  }

  const languages = Array.isArray(req.languages)
    ? req.languages.slice(0, 5).map(String)
    : ['en'];

  return { url, languages };
}

function corsHeaders(origin: string): Record<string, string> {
  return {
    'Access-Control-Allow-Origin': ALLOWED_ORIGINS.includes(origin) ? origin : 'null',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
  };
}

async function handleTranscript(req: TranscriptRequest): Promise<TranscriptResponse> {
  const videoId = extractVideoId(req.url);
  if (!videoId) throw new TranscriptError('INVALID_YOUTUBE_URL');

  const metadata = await fetchMetadata(videoId);
  const captions = await captionsTimed(videoId, req.languages);
  const segments = groupTranscript(captions);

  const snippets = captions.map((c, i) => ({
    id: `cap-${i}`,
    text: c.text,
  }));

  return {
    video: {
      ...metadata,
      url: `https://www.youtube.com/watch?v=${videoId}`,
    },
    language: 'English',
    languageCode: 'en',
    generated: false,
    snippets,
    segments: segments.map((s) => ({
      id: s.id,
      text: s.text,
      start: s.start,
      end: s.end,
    })),
  };
}

serve(async (req: Request) => {
  const origin = req.headers.get('origin') || '';
  const headers = corsHeaders(origin);

  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers });
  }

  if (req.method !== 'POST') {
    return new Response(
      JSON.stringify({ error: 'Method not allowed' }),
      { status: 405, headers },
    );
  }

  try {
    const body = await req.json();
    const request = validateRequest(body);
    const response = await handleTranscript(request);
    return new Response(JSON.stringify(response), { status: 200, headers });
  } catch (error) {
    if (error instanceof TranscriptError) {
      return error.toResponse();
    }
    const err = new TranscriptError('UPSTREAM_ERROR');
    return err.toResponse();
  }
});
