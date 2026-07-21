import { create } from "zustand";

type MaterialSelectionStore = {
  selectedIds: string[];
  pinnedIds: string[];
  copiedNodeId: string | null;
  clearSelectedIds: () => void;
  setSelectedIds: (ids: string[]) => void;
  setPinnedIds: (ids: string[]) => void;
  togglePinned: (id: string) => void;
  setCopiedNode: (id: string | null) => void;
  toggleSelectedBranch: (branchIds: string[]) => void;
};

export const useMaterialSelectionStore = create<MaterialSelectionStore>(
  (set, get) => ({
    selectedIds: [],
    pinnedIds: [],
    copiedNodeId: null,
    clearSelectedIds: () => set({ selectedIds: [] }),
    setSelectedIds: (ids) => set({ selectedIds: [...new Set(ids)] }),
    setPinnedIds: (ids) => set({ pinnedIds: [...new Set(ids)].slice(0, 50) }),
    togglePinned: (id) => set((state) => ({ pinnedIds: state.pinnedIds.includes(id) ? state.pinnedIds.filter((item) => item !== id) : [...state.pinnedIds, id].slice(0, 50) })),
    setCopiedNode: (copiedNodeId) => set({ copiedNodeId }),
    toggleSelectedBranch: (branchIds) => {
      const branch = [...new Set(branchIds)];
      const current = get().selectedIds;
      if (branch.every((id) => current.includes(id))) {
        const removed = new Set(branch);
        set({ selectedIds: current.filter((id) => !removed.has(id)) });
        return;
      }
      const additions = branch.filter((id) => !current.includes(id));
      set({ selectedIds: [...current, ...additions] });
    },
  }),
);
