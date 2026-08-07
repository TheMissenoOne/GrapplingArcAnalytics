import { assertEquals } from 'https://deno.land/std@0.208.0/assert/mod.ts';
import { groupTranscript } from '../grouping.ts';
import { parseCaptionPayload } from '../youtube.ts';

Deno.test('contract: basic caption parsing', () => {
  const events = [
    {
      tStartMs: 0,
      durationMs: 1000,
      segs: [{ utf8: 'hello' }, { utf8: ' world' }],
    },
  ];
  const result = parseCaptionPayload(events as Array<Record<string, unknown>>);
  assertEquals(result.length, 1);
  assertEquals(result[0].text, 'hello world');
  assertEquals(result[0].start, 0);
  assertEquals(result[0].duration, 1);
});

Deno.test('contract: empty events array', () => {
  const result = parseCaptionPayload([]);
  assertEquals(result, []);
});

Deno.test('contract: missing timestamps', () => {
  const events = [
    { segs: [{ utf8: 'hello' }] },
  ];
  const result = parseCaptionPayload(events as Array<Record<string, unknown>>);
  assertEquals(result, []);
});

Deno.test('contract: empty text skipped', () => {
  const events = [
    {
      tStartMs: 0,
      durationMs: 1000,
      segs: [{ utf8: '   ' }, { utf8: '' }],
    },
    {
      tStartMs: 1000,
      durationMs: 1000,
      segs: [{ utf8: 'real text' }],
    },
  ];
  const result = parseCaptionPayload(events as Array<Record<string, unknown>>);
  assertEquals(result.length, 1);
  assertEquals(result[0].text, 'real text');
});

Deno.test('contract: response shape', () => {
  const captions = [
    { text: 'intro', start: 0, duration: 2 },
    { text: 'main content', start: 2, duration: 10 },
  ];
  const segments = groupTranscript(captions);

  for (const seg of segments) {
    assertEquals(typeof seg.id, 'string');
    assertEquals(typeof seg.text, 'string');
    assertEquals(typeof seg.start, 'number');
    assertEquals(typeof seg.end, 'number');
    assertEquals(Array.isArray(seg.captionIds), true);
  }
});
