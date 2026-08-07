import { Caption, Segment } from './grouping.ts';
import { TranscriptError } from './errors.ts';

export interface VideoMetadata {
  id: string;
  title: string;
  channel: string;
  thumbnail: string;
  duration: number | null;
}

interface CaptionTrack {
  baseUrl: string;
  vssId: string;
  isGenerated: boolean;
}

const INNERTUBE_API = 'https://www.youtube.com/youtubei/v1/get_transcript';
const INNERTUBE_KEY = 'AIzaSyAO90d0o_cTsr0_Xi6SJlSluqrewYXHWAI';

export function extractVideoId(value: string): string | null {
  const patterns = [
    /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/|youtube\.com\/shorts\/|youtube\.com\/live\/)([a-zA-Z0-9_-]{11})/,
    /^([a-zA-Z0-9_-]{11})$/,
  ];

  for (const pattern of patterns) {
    const match = value.match(pattern);
    if (match) return match[1];
  }
  return null;
}

export async function fetchMetadata(videoId: string): Promise<VideoMetadata> {
  try {
    const response = await fetch(`https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=${videoId}&format=json`);
    if (!response.ok) throw new TranscriptError('METADATA_ERROR');

    const data = await response.json() as Record<string, unknown>;
    return {
      id: videoId,
      title: String(data.title ?? ''),
      channel: String(data.author_name ?? ''),
      thumbnail: String(data.thumbnail_url ?? ''),
      duration: null,
    };
  } catch {
    throw new TranscriptError('METADATA_ERROR');
  }
}

export async function discoverCaptionTracks(videoId: string): Promise<CaptionTrack[]> {
  try {
    const response = await fetch(`https://www.youtube.com/watch?v=${videoId}`, {
      headers: { 'User-Agent': 'Mozilla/5.0' },
    });
    if (!response.ok) throw new TranscriptError('VIDEO_UNAVAILABLE');

    const html = await response.text();
    const captionTracksMatch = html.match(/"captionTracks":\s*(\[.*?\])/);
    if (!captionTracksMatch) return [];

    const tracks = JSON.parse(captionTracksMatch[1]) as Array<Record<string, unknown>>;
    return tracks.map((t) => ({
      baseUrl: String(t.baseUrl ?? ''),
      vssId: String(t.vssId ?? ''),
      isGenerated: t.kind === 'asr',
    })).filter((t) => t.baseUrl);
  } catch (e) {
    if (e instanceof TranscriptError) throw e;
    return [];
  }
}

export function selectCaptionTrack(tracks: CaptionTrack[], languages: string[] = ['en']): CaptionTrack | null {
  if (!tracks.length) return null;

  const manual = tracks.filter((t) => !t.isGenerated);
  const candidates = manual.length ? manual : tracks;

  for (const lang of languages) {
    const match = candidates.find((t) => t.vssId.startsWith(lang));
    if (match) return match;
  }

  return candidates[0] ?? null;
}

export async function fetchCaptionPayload(baseUrl: string): Promise<Caption[]> {
  try {
    const url = new URL(baseUrl);
    url.searchParams.set('fmt', 'json3');
    const response = await fetch(url.toString());
    if (!response.ok) throw new Error();

    const data = await response.json() as Record<string, unknown>;
    const events = data.events as Array<Record<string, unknown>> | undefined;
    return parseCaptionPayload(events ?? []);
  } catch {
    throw new TranscriptError('UPSTREAM_ERROR');
  }
}

export function parseCaptionPayload(events: Array<Record<string, unknown>>): Caption[] {
  const captions: Caption[] = [];

  for (const event of events) {
    if (event.tStartMs == null || event.durationMs == null) continue;

    const segs = event.segs as Array<Record<string, unknown>> | undefined;
    if (!segs) continue;

    const text = segs.map((s) => String(s.utf8 ?? '')).join('');
    if (!text.trim()) continue;

    captions.push({
      text: text.trim(),
      start: Number(event.tStartMs) / 1000,
      duration: Number(event.durationMs) / 1000,
    });
  }

  return captions;
}

export async function captionsTimed(videoId: string, languages: string[] = ['en']): Promise<Caption[]> {
  const tracks = await discoverCaptionTracks(videoId);
  if (!tracks.length) throw new TranscriptError('NO_CAPTIONS');

  const track = selectCaptionTrack(tracks, languages);
  if (!track) throw new TranscriptError('NO_CAPTIONS');

  return fetchCaptionPayload(track.baseUrl);
}
