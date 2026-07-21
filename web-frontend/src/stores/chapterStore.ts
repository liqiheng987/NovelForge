import { create } from "zustand";
import type { Chapter } from "../types";

type ChapterStore = {
  chapters: Chapter[];
  selectedChapterId: string | null;
  setChapters: (chapters: Chapter[]) => void;
  selectChapter: (chapterId: string | null) => void;
};

export const useChapterStore = create<ChapterStore>((set) => ({
  chapters: [],
  selectedChapterId: null,
  setChapters: (chapters) => set({ chapters }),
  selectChapter: (selectedChapterId) => set({ selectedChapterId }),
}));
