export type ErrorCode =
  | 'INVALID_YOUTUBE_URL'
  | 'VIDEO_UNAVAILABLE'
  | 'NO_CAPTIONS'
  | 'PO_TOKEN_REQUIRED'
  | 'YOUTUBE_BLOCKED'
  | 'UPSTREAM_RATE_LIMIT'
  | 'UPSTREAM_ERROR'
  | 'METADATA_ERROR';

const errorMap: Record<ErrorCode, { status: number; message: string }> = {
  INVALID_YOUTUBE_URL: { status: 400, message: 'Invalid YouTube URL' },
  VIDEO_UNAVAILABLE: { status: 404, message: 'Video not found or unavailable' },
  NO_CAPTIONS: { status: 422, message: 'No captions available for this video' },
  PO_TOKEN_REQUIRED: { status: 503, message: 'YouTube requires authentication' },
  YOUTUBE_BLOCKED: { status: 503, message: 'YouTube access blocked' },
  UPSTREAM_RATE_LIMIT: { status: 429, message: 'YouTube rate limit exceeded' },
  UPSTREAM_ERROR: { status: 502, message: 'YouTube server error' },
  METADATA_ERROR: { status: 502, message: 'Failed to fetch video metadata' },
};

export class TranscriptError extends Error {
  constructor(
    readonly code: ErrorCode,
    message?: string,
  ) {
    super(message || errorMap[code].message);
    this.name = 'TranscriptError';
  }

  toResponse(): Response {
    const { status, message } = errorMap[this.code];
    return new Response(
      JSON.stringify({ error: this.code, message }),
      { status, headers: { 'Content-Type': 'application/json' } },
    );
  }
}
