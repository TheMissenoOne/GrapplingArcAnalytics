export interface Caption {
  text: string;
  start: number;
  duration: number;
}

export interface Segment {
  id: string;
  text: string;
  start: number;
  end: number;
  captionIds: number[];
}

const TARGET_CHARS = 1100;
const MAX_SECONDS = 120;

export function groupTranscript(captions: Caption[]): Segment[] {
  if (!captions.length) return [];

  const segments: Segment[] = [];
  let currentSegment = {
    captionIds: [] as number[],
    text: [] as string[],
    startTime: captions[0].start,
  };

  for (let i = 0; i < captions.length; i++) {
    const caption = captions[i];
    const tentativeText = [...currentSegment.text, caption.text].join(' ');
    const currentDuration = caption.start + caption.duration - currentSegment.startTime;
    const wouldExceedChar = tentativeText.length > TARGET_CHARS;
    const wouldExceedTime = currentDuration > MAX_SECONDS;

    if (currentSegment.captionIds.length > 0 && (wouldExceedChar || wouldExceedTime)) {
      segments.push({
        id: `seg-${segments.length}`,
        text: currentSegment.text.join(' '),
        start: currentSegment.startTime,
        end: captions[i - 1].start + captions[i - 1].duration,
        captionIds: [...currentSegment.captionIds],
      });

      currentSegment = {
        captionIds: [],
        text: [],
        startTime: caption.start,
      };
    }

    currentSegment.captionIds.push(i);
    currentSegment.text.push(caption.text);
  }

  if (currentSegment.captionIds.length > 0) {
    const lastCaption = captions[captions.length - 1];
    segments.push({
      id: `seg-${segments.length}`,
      text: currentSegment.text.join(' '),
      start: currentSegment.startTime,
      end: lastCaption.start + lastCaption.duration,
      captionIds: [...currentSegment.captionIds],
    });
  }

  return segments;
}
