import { assertEquals } from 'https://deno.land/std@0.208.0/assert/mod.ts';
import { groupTranscript } from '../grouping.ts';

Deno.test('grouping: empty captions', () => {
  const result = groupTranscript([]);
  assertEquals(result, []);
});

Deno.test('grouping: single caption', () => {
  const result = groupTranscript([
    { text: 'hello world', start: 0, duration: 2 },
  ]);
  assertEquals(result.length, 1);
  assertEquals(result[0].text, 'hello world');
  assertEquals(result[0].start, 0);
  assertEquals(result[0].end, 2);
});

Deno.test('grouping: never splits caption mid-text', () => {
  const longText = 'a'.repeat(2000);
  const result = groupTranscript([
    { text: longText, start: 0, duration: 1 },
  ]);
  assertEquals(result.length, 1);
  assertEquals(result[0].text, longText);
});

Deno.test('grouping: char limit enforced', () => {
  const result = groupTranscript([
    { text: 'x'.repeat(600), start: 0, duration: 10 },
    { text: 'y'.repeat(600), start: 10, duration: 10 },
    { text: 'z'.repeat(600), start: 20, duration: 10 },
  ]);
  assertEquals(result.length >= 2, true);
});

Deno.test('grouping: duration limit enforced', () => {
  const captions = Array.from({ length: 150 }, (_, i) => ({
    text: `word${i}`,
    start: i,
    duration: 1,
  }));
  const result = groupTranscript(captions);
  assertEquals(result.length >= 2, true);
  for (const seg of result) {
    const duration = seg.end - seg.start;
    assertEquals(duration <= 121, true);
  }
});

Deno.test('grouping: preserves order', () => {
  const result = groupTranscript([
    { text: 'a', start: 0, duration: 1 },
    { text: 'b', start: 1, duration: 1 },
    { text: 'c', start: 2, duration: 1 },
  ]);
  assertEquals(result.length, 1);
  assertEquals(result[0].text, 'a b c');
});
