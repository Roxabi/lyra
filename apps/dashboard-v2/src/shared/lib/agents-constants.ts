export const SOUL_SECTIONS = [
  "Identity",
  "Personality",
  "Values",
  "Expertise",
  "Guidelines",
] as const;

export type SoulSectionName = (typeof SOUL_SECTIONS)[number];
