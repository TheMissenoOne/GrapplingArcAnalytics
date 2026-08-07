import { assertEquals } from 'https://deno.land/std@0.208.0/assert/mod.ts';
import { extractVideoId } from '../youtube.ts';

Deno.test('youtube: raw 11-char ID', () => {
  assertEquals(extractVideoId('dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
});

Deno.test('youtube: watch?v= format', () => {
  assertEquals(extractVideoId('https://www.youtube.com/watch?v=dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
});

Deno.test('youtube: youtu.be format', () => {
  assertEquals(extractVideoId('https://youtu.be/dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
});

Deno.test('youtube: embed format', () => {
  assertEquals(extractVideoId('https://www.youtube.com/embed/dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
});

Deno.test('youtube: shorts format', () => {
  assertEquals(extractVideoId('https://www.youtube.com/shorts/dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
});

Deno.test('youtube: live format', () => {
  assertEquals(extractVideoId('https://www.youtube.com/live/dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
});

Deno.test('youtube: URL with timestamp', () => {
  assertEquals(extractVideoId('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s'), 'dQw4w9WgXcQ');
});

Deno.test('youtube: invalid format', () => {
  assertEquals(extractVideoId('https://example.com/video'), null);
  assertEquals(extractVideoId('not-a-video-id'), null);
  assertEquals(extractVideoId(''), null);
});
